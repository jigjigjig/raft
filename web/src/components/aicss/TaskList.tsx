import { Check, Circle, LoaderCircle } from "lucide-react";

type Task = { label: string; state: "pending" | "active" | "complete" };

// AICSS TodoList visual language, bound to Raft's real persisted run state.
export function TaskList({ tasks, completed, total }: { tasks: Task[]; completed: number; total: number }) {
  return (
    <div className="task-list">
      <div className="task-list-head">
        <span>Analysis plan</span>
        <span className="mono">{completed.toLocaleString()} / {total.toLocaleString()}</span>
      </div>
      <ol>
        {tasks.map((task) => (
          <li key={task.label} className={task.state}>
            {task.state === "complete" ? <Check size={15} /> : task.state === "active" ? <LoaderCircle className="spin" size={15} /> : <Circle size={15} />}
            <span>{task.label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
