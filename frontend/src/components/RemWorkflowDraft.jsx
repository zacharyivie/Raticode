import { useMemo, useState } from "react";
import { Workflow } from "lucide-react";
import { workflowDraftSummary } from "../lib/remHome.js";
import { previewRemWorkflowSwitch, switchRemWorkflow } from "../lib/remHomeStore.js";
import { ChangesReview } from "./RattishTriggersSection.jsx";

// Make it a workflow: the drafting task's finished card. It compares the repeating task's
// average cost per run with the workflow's test run, and offers Switch over. Switching
// reviews the workflow's files first and approves exactly that version, so nothing
// switches without the user.
export function WorkflowDraftCard({ task }) {
  const result = task?.workflow_draft_result;
  const summary = workflowDraftSummary(result);
  const [reviewing, setReviewing] = useState(false);
  const [plan, setPlan] = useState(null);
  // Stable for one task, so the review loads its changes once.
  const taskId = task?.id;
  const source = useMemo(() => ({
    changes: async () => {
      const preview = await previewRemWorkflowSwitch({ id: taskId });
      setPlan(preview);
      return preview.changes;
    },
    approve: (fingerprint, options) => switchRemWorkflow({ id: taskId }, fingerprint, options),
  }), [taskId]);
  if (!summary) return null;
  const title = result.source_title ? `“${result.source_title}”` : "the task";
  const intro = plan
    ? `Switch over adds a cron trigger (${plan.cron}, ${plan.timezone}), the same schedule as ${title}: ${plan.schedule}. It approves this version of the workflow and pauses the task's schedule. Resume schedule on the task undoes it.`
    : "";
  return <section className="rem-workflow-draft" aria-label="Workflow draft" data-tone={summary.tone}>
    <p className="rem-workflow-draft-title"><Workflow size={14} aria-hidden="true" /><strong>{summary.title}</strong></p>
    {summary.lines.map(line => <p key={line} className="rem-workflow-draft-note">{line}</p>)}
    {summary.path ? <p className="rem-workflow-draft-path" title={summary.path}>{summary.path}</p> : null}
    {summary.costs ? <dl className="rem-workflow-costs">
      <div><dt>Rem task</dt><dd>{summary.costs.task}</dd></div>
      <div><dt>Workflow</dt><dd>{summary.costs.workflow}</dd></div>
    </dl> : null}
    {summary.saving ? <p className="rem-workflow-draft-note">{summary.saving}</p> : null}
    {summary.switched ? <p className="rem-workflow-draft-note" role="status">Switched over. The workflow's cron trigger runs it on {title}'s schedule, and the task's schedule is paused. Resume schedule on the task undoes it.</p>
      : summary.canSwitch && result.status === "ready" ? reviewing
        ? <ChangesReview source={source} intro={intro} approveLabel="Approve and switch over" onApproved={() => setReviewing(false)} onClose={() => setReviewing(false)} />
        : <div className="rem-result-actions" role="group" aria-label="Workflow draft actions">
          <button type="button" data-action="switch-over" onClick={() => setReviewing(true)}>Switch over…</button>
        </div>
      : null}
  </section>;
}

// Under the newest update about a finished drafting task in Rem's chat.
export function WorkflowDraftForMessage({ message, tasks = [], live }) {
  if (!live || message?.kind !== "task_update" || !message.work) return null;
  const task = tasks.find(item => item.id === message.work.task_id);
  if (!task?.workflow_draft_result || task.deleted_at || task.last_run?.id !== message.work.run_id) return null;
  return <WorkflowDraftCard task={task} />;
}
