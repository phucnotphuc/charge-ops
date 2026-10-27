import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../api/auth";
import { ErrorBox } from "../components/Async";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err), null));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack narrow">
      <h1>Đăng nhập operator</h1>
      <form className="card form" onSubmit={submit}>
        <label>
          Tên đăng nhập
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          Mật khẩu
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        <button type="submit" className="btn primary" disabled={busy}>
          Đăng nhập
        </button>
      </form>
      {error && <ErrorBox error={error} />}
    </section>
  );
}
