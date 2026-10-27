import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "./client";
import type { Role } from "./types";

interface AuthState {
  role: Role;
  username: string | null;
  ready: boolean;
  /** Set when /auth/me failed for a reason other than "not signed in". */
  error: ApiError | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  role: "viewer",
  username: null,
  ready: true,
  error: null,
  login: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<Role>("viewer");
  const [username, setUsername] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .me()
      .then((me) => {
        if (!alive) return;
        setRole(me.role);
        setUsername(me.username);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        // 401/403 means anonymous viewer; anything else is a real problem to show.
        if (!(e instanceof ApiError) || (e.status !== 401 && e.status !== 403)) {
          setError(e instanceof ApiError ? e : null);
        }
      })
      .finally(() => alive && setReady(true));
    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(async (user: string, password: string) => {
    const me = await api.login(user, password);
    setRole(me.role);
    setUsername(me.username);
    setError(null);
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setRole("viewer");
    setUsername(null);
  }, []);

  const value = useMemo(
    () => ({ role, username, ready, error, login, logout }),
    [role, username, ready, error, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
