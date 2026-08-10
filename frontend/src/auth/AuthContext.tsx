/**
 * Global auth state. Holds the current user in React state and the access token
 * in memory (via the api client). On mount it attempts a silent refresh so a
 * returning user with a valid refresh cookie stays logged in across reloads.
 */
import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  refreshAccessToken,
  setAccessToken,
  setOnAuthFailure,
} from "../api/client";
import * as authApi from "../api/auth";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  initializing: boolean;
  register: (username: string, email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [initializing, setInitializing] = useState(true);

  const clearAuth = useCallback(() => {
    setAccessToken(null);
    setUser(null);
  }, []);

  // If the refresh interceptor ultimately fails, drop to logged-out state.
  useEffect(() => {
    setOnAuthFailure(clearAuth);
  }, [clearAuth]);

  // Silent bootstrap: try to restore a session from the httpOnly refresh cookie.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Go through the shared, coalesced refresh so that StrictMode's
        // double-invoked mount effect (and any other concurrent caller)
        // results in a single /auth/refresh network request. The token is
        // set inside refreshAccessToken().
        await refreshAccessToken();
        const me = await authApi.fetchMe();
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) clearAuth();
      } finally {
        if (!cancelled) setInitializing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clearAuth]);

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      const res = await authApi.register(username, email, password);
      setAccessToken(res.access_token);
      setUser(res.user);
    },
    [],
  );

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password);
    setAccessToken(res.access_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      clearAuth();
    }
  }, [clearAuth]);

  const value = useMemo(
    () => ({ user, initializing, register, login, logout }),
    [user, initializing, register, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
