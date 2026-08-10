import { useAuth } from "../auth/useAuth";

export function Home() {
  const { user, logout } = useAuth();

  return (
    <div className="page">
      <header className="topbar">
        <strong>Home Feed</strong>
        <div className="spacer" />
        <span className="muted">@{user?.username}</span>
        <button className="ghost" onClick={() => void logout()}>
          Log out
        </button>
      </header>

      <main className="feed">
        <div className="card">
          <h2>You're in, {user?.profile?.display_name || user?.username} 👋</h2>
          <p className="muted">
            This is a placeholder feed. Auth is wired end-to-end: your access
            token lives in memory and refreshes silently in the background.
          </p>
        </div>
      </main>
    </div>
  );
}
