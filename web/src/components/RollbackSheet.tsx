import type { RestoreMode } from "../protocol";
import { Icon } from "../icons";

export interface RollbackTarget {
  sessionId: string;
  checkpointId?: string;
  numTurns: number;
  label: string;
}

export function RollbackSheet({ target, onClose, onConfirm }: {
  target: RollbackTarget | null;
  onClose: () => void;
  onConfirm: (mode: RestoreMode) => void;
}) {
  if (!target) return null;
  const options: { mode: RestoreMode; icon: string; title: string; detail: string }[] = [
    {
      mode: "conversation", icon: "history", title: "仅回滚对话",
      detail: `移除最近 ${target.numTurns} 轮对话，保留当前文件。`,
    },
    {
      mode: "files", icon: "code", title: "仅恢复代码",
      detail: `恢复最近 ${target.numTurns} 轮由 Remote 记录的 Git 工作树改动；冲突时不会覆盖。`,
    },
    {
      mode: "both", icon: "refresh", title: "对话和代码",
      detail: "先安全恢复代码，再回滚对话；两部分结果会分别确认。",
    },
  ];
  return <>
    <div className="scrim show" onClick={onClose} />
    <section className="sheet show rollback-sheet" role="dialog" aria-modal="true"
      aria-label="选择回滚范围">
      <div className="sheet-grip" />
      <header className="rollback-head">
        <span className="rollback-head-icon"><Icon name="history" size={19} /></span>
        <span><b>Codex 回滚</b><small>{target.label}</small></span>
        <button onClick={onClose} aria-label="关闭"><Icon name="close" size={17} /></button>
      </header>
      <div className="rollback-options">
        {options.map((option) => (
          <button key={option.mode} onClick={() => onConfirm(option.mode)}>
            <span><Icon name={option.icon} size={17} /></span>
            <span><b>{option.title}</b><small>{option.detail}</small></span>
            <Icon name="chev" size={15} />
          </button>
        ))}
      </div>
      <p className="rollback-warning">回滚是破坏性操作。若检测到回滚点之后的代码改动，Remote 会拒绝覆盖并保留现状。</p>
    </section>
  </>;
}
