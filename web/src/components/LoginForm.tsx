import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon, ClaudeMark } from "../icons";
import { useImeSubmit } from "../use-ime-submit";

export function LoginForm({
  onLogin, theme, onToggleTheme,
}: {
  onLogin: () => void;
  theme: string;
  onToggleTheme: () => void;
}) {
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [multiUser, setMultiUser] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const pendingPasswordSelection = useRef<{
    start: number | null;
    end: number | null;
    direction: "forward" | "backward" | "none" | null;
    restoreFocus: boolean;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/auth-config", {
      credentials: "same-origin", cache: "no-store",
    }).then(async (response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (!cancelled) setMultiUser(payload?.multi_user === true);
      }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const submit = async (value = password) => {
    if (!value) return;
    setError("");
    setLoading(true);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password: value }),
      });
      if (r.status === 429) { setError("尝试太频繁，等一分钟再试"); return; }
      if (!r.ok) { setError(multiUser ? "账号或密码错误" : "密码错误"); return; }
      onLogin();
    } catch {
      setError("网络错误");
    } finally {
      setLoading(false);
    }
  };
  const imeSubmit = useImeSubmit<HTMLInputElement>((value) => { void submit(value); });
  const passwordInputRef = imeSubmit.inputRef;

  useLayoutEffect(() => {
    const selection = pendingPasswordSelection.current;
    const input = passwordInputRef.current;
    if (!selection || !input) {
      pendingPasswordSelection.current = null;
      return;
    }
    const restoreSelection = () => {
      if (selection.restoreFocus) input.focus({ preventScroll: true });
      if (selection.start !== null && selection.end !== null) {
        input.setSelectionRange(
          selection.start, selection.end, selection.direction ?? undefined);
      }
    };
    restoreSelection();
    // Safari can reset the caret again while committing the input type change.
    // Repeat after that browser-native update instead of relying on layout timing.
    const frame = window.requestAnimationFrame(() => {
      restoreSelection();
      if (pendingPasswordSelection.current === selection) {
        pendingPasswordSelection.current = null;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [passwordVisible, passwordInputRef]);

  const rememberPasswordSelection = () => {
    const input = passwordInputRef.current;
    pendingPasswordSelection.current = {
      start: input?.selectionStart ?? null,
      end: input?.selectionEnd ?? null,
      direction: input?.selectionDirection ?? null,
      restoreFocus: document.activeElement === input,
    };
  };

  const togglePasswordVisibility = () => {
    // Keyboard activation has no pointerdown, so retain an onClick fallback.
    if (!pendingPasswordSelection.current) rememberPasswordSelection();
    setPasswordVisible((visible) => !visible);
  };

  return (
    <div className="login">
      <button className="iconbtn tt" onClick={onToggleTheme} aria-label="切换主题">
        <Icon name={theme === "dark" ? "sun" : "moon"} />
      </button>
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark"><ClaudeMark size={30} /></span>
          <span className="name"><b>cc</b><span>·remote</span></span>
        </div>
        <p className="login-tag serif" style={{ fontSize: 15 }}>你的 Claude Code，随身遥控</p>
        {multiUser && <div className="login-field">
          <Icon name="user" size={18} />
          <input
            type="text"
            name="username"
            placeholder="账号"
            value={username}
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="next"
            onChange={(event) => setUsername(event.target.value)}
            disabled={loading}
            autoFocus
          />
        </div>}
        <div className="login-field">
          <Icon name="lock" size={18} />
          <input
            ref={passwordInputRef}
            type={passwordVisible ? "text" : "password"}
            name="password"
            placeholder="访问密码"
            value={password}
            autoComplete="current-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
            onChange={(e) => setPassword(e.target.value)}
            onCompositionStart={imeSubmit.startComposition}
            onCompositionEnd={(e) => {
              imeSubmit.endComposition();
              setPassword(e.currentTarget.value);
            }}
            onKeyDown={(e) => {
              if (!imeSubmit.shouldSubmitKey({
                key: e.key,
                shiftKey: e.shiftKey,
                isComposing: e.nativeEvent.isComposing,
                keyCode: e.nativeEvent.keyCode,
              })) return;
              e.preventDefault();
              imeSubmit.requestSubmit();
            }}
            disabled={loading}
            autoFocus={!multiUser}
          />
          <button type="button" className="login-reveal"
            aria-label={passwordVisible ? "隐藏密码" : "显示密码"}
            aria-pressed={passwordVisible}
            title={passwordVisible ? "隐藏密码，恢复密码输入模式" : "显示密码"}
            onPointerDown={(event) => {
              rememberPasswordSelection();
              event.preventDefault();
            }}
            onClick={togglePasswordVisibility}
            disabled={loading}>
            <Icon name={passwordVisible ? "eye-off" : "eye"} size={19} />
          </button>
        </div>
        {error && <div className="login-err">{error}</div>}
        <button className="login-btn"
          onPointerDown={imeSubmit.commitCompositionBeforePointerSubmit}
          onClick={imeSubmit.requestSubmit}
          disabled={loading || !password || (multiUser && !username)}>
          {loading ? "登录中…" : "进入"}
        </button>
      </div>
    </div>
  );
}
