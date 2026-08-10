import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./useAuth";

/** Gate for authenticated-only routes. */
export function ProtectedRoute() {
  const { user, initializing } = useAuth();

  if (initializing) {
    return <div className="centered muted">Loading…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
