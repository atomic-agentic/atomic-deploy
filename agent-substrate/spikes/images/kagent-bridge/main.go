// SPDX-License-Identifier: Apache-2.0

// Spike 6: the smallest kagent bring-your-own harness that behaves like
// atomic's bridge would at a gate. Built on kagent's Go SDK at v1.0.1, after
// go/core/test/byoa2a.
//
// The first message on a task "starts a workflow run" (a stub: no atomic
// serve yet) and pauses at a gate, as kagent's human-in-the-loop extension
// expects: an input-required status carrying a tool_approval_request. The
// answer on the same task is validated against the pending request and
// reported with everything the harness can see about who answered.
package main

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"iter"
	"net/http"
	"os"
	"strings"
	"time"

	a2atype "github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"

	"github.com/kagent-dev/kagent/go/adk/pkg/app"
	"github.com/kagent-dev/kagent/go/adk/pkg/config"
	apia2a "github.com/kagent-dev/kagent/go/api/a2a"
	"github.com/kagent-dev/kagent/go/pkg/logging"
)

const gateID = "gate-send-reply"

type bridge struct{}

func reply(request *a2atype.TaskInfoProvider, text string) *a2atype.Message {
	message := a2atype.NewMessage(a2atype.MessageRoleAgent, a2atype.NewTextPart(text))
	info := (*request).TaskInfo()
	message.ContextID, message.TaskID = info.ContextID, info.TaskID
	return message
}

// caller reports what the harness is told about the person who sent this
// message: the A2A user and any identity headers among the service params.
func caller(request *a2asrv.ExecutorContext) string {
	var parts []string
	if request.User != nil {
		parts = append(parts, fmt.Sprintf("user=%+v", *request.User))
	}
	if request.ServiceParams != nil {
		for _, key := range []string{"x-user-id", "authorization", "ate-target-actor"} {
			if values, ok := request.ServiceParams.Get(key); ok {
				shown := strings.Join(values, ",")
				if key == "authorization" {
					shown = fmt.Sprintf("present (%d chars)", len(shown))
				}
				parts = append(parts, key+"="+shown)
			}
		}
	}
	return strings.Join(parts, " ")
}

// forward sends the decision to atomic serve (ATOMIC_URL) the way the real
// bridge would. The Authorization header carries a placeholder: Substrate's
// egress gateway replaces it with an actor JWT when the agent's EgressPolicy
// grants one for that origin, so no credential is ever in the actor.
func forward(request *a2asrv.ExecutorContext, d apia2a.ToolApproval) string {
	target := os.Getenv("ATOMIC_URL")
	if target == "" {
		return "No ATOMIC_URL: nothing forwarded."
	}
	body, _ := json.Marshal(map[string]any{
		"task": request.TaskID, "context": request.ContextID, "gate": d.ID,
		"approved": d.Approved, "reason": d.RejectionReason, "principal": caller(request),
	})
	req, err := http.NewRequest(http.MethodPost, target+"/v1/gates/decisions", bytes.NewReader(body))
	if err != nil {
		return "forward: " + err.Error()
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer kagent-placeholder")
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return "forward failed: " + err.Error()
	}
	defer resp.Body.Close()
	echoed, _ := io.ReadAll(io.LimitReader(resp.Body, 8192))
	return fmt.Sprintf("Forwarded to %s: HTTP %d. atomic serve stand-in saw: %s", target, resp.StatusCode, echoed)
}

func (bridge) Execute(_ context.Context, request *a2asrv.ExecutorContext) iter.Seq2[a2atype.Event, error] {
	return func(yield func(a2atype.Event, error) bool) {
		var info a2atype.TaskInfoProvider = request
		fmt.Fprintf(os.Stderr, "execute task=%s context=%s caller: %s\n", request.TaskID, request.ContextID, caller(request))

		// The answer to a pending gate.
		if task := request.StoredTask; task != nil && task.Status.State == a2atype.TaskStateInputRequired {
			pending, err := apia2a.ParseToolApprovalRequest(task.Status.Message)
			if err == nil {
				var decided *apia2a.ToolApprovalResponse
				decided, err = apia2a.ParseToolApprovalResponse(request.Message)
				if err == nil {
					err = apia2a.ValidateToolApprovalResponse(pending, decided)
				}
				if err == nil {
					d := decided.Approvals[0]
					text := fmt.Sprintf("Gate %s decided: approved=%v reason=%q. Caller as the harness sees it: %s. ",
						d.ID, d.Approved, d.RejectionReason, caller(request))
					text += forward(request, d)
					yield(a2atype.NewStatusUpdateEvent(request, a2atype.TaskStateCompleted, reply(&info, text)), nil)
					return
				}
			}
			yield(a2atype.NewStatusUpdateEvent(request, a2atype.TaskStateInputRequired,
				reply(&info, "That was not a decision for the pending gate: "+err.Error())), nil)
			return
		}

		// A new task: start a run, which pauses at its first gate.
		if !yield(a2atype.NewSubmittedTask(request, request.Message), nil) {
			return
		}
		if !yield(a2atype.NewStatusUpdateEvent(request, a2atype.TaskStateWorking,
			reply(&info, "Started atomic workflow run stub-run-1 (a stub: no atomic serve yet).")), nil) {
			return
		}
		gate := reply(&info, "Approve sending the draft reply? (atomic.gate)")
		if err := apia2a.AttachHITL(gate, apia2a.ToolApprovalRequest{
			Type:  apia2a.HITLTypeToolApprovalRequest,
			Hint:  "Approve sending the draft reply?",
			Tools: []apia2a.HITLTool{{ID: gateID, CallID: gateID, Name: "atomic.gate", Args: map[string]any{"run": "stub-run-1", "gate": "send-reply"}}},
		}); err != nil {
			yield(nil, err)
			return
		}
		yield(a2atype.NewStatusUpdateEvent(request, a2atype.TaskStateInputRequired, gate), nil)
	}
}

func (bridge) Cancel(context.Context, *a2asrv.ExecutorContext) iter.Seq2[a2atype.Event, error] {
	return func(func(a2atype.Event, error) bool) {}
}

func main() {
	if err := config.MaterializeFromBootstrap(context.Background(), "/config"); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	compiled, err := config.LoadAgentConfig("/config/config.json")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	runtime := config.ProcessConfig(compiled)
	logger, err := logging.New(os.Stderr, cmp.Or(runtime.LogLevel, "info"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	application, err := app.New(app.AppConfig{ControlPlaneURL: runtime.ControlPlaneUrl, MaxContentLength: runtime.MaxContentLength,
		AgentCard: a2atype.AgentCard{Name: "atomic-bridge-spike", Version: "v1",
			Capabilities: a2atype.AgentCapabilities{Streaming: true, Extensions: []a2atype.AgentExtension{apia2a.HITLExtension()}}},
		Port:    fmt.Sprint(cmp.Or(runtime.GetPort(), 80)),
		AppName: cmp.Or(runtime.Name, "atomic-bridge-spike"), Logger: logger,
	}, bridge{})
	if err != nil {
		logger.Error("failed to create A2A application", "error", err)
		os.Exit(1)
	}
	if err := application.Run(); err != nil {
		logger.Error("A2A application stopped", "error", err)
		os.Exit(1)
	}
}
