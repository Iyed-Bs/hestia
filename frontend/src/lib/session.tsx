// The signed-in user. The session cookie is HttpOnly, so the page asks the
// gateway who it is (/api/auth/me) and gets the CSRF token in the answer.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, post, setCsrf } from "./api";
import type { Me, Role } from "./types";

interface Session {
  me: Me | null;
  checked: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (role: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };
const SessionContext = createContext<Session | null>(null);

// Ask the gateway who we are. Also refreshes the in-memory CSRF token.
async function fetchMe(): Promise<Me | null> {
  try {
    const info = await api<Me>("/api/auth/me");
    setCsrf(info.csrf);
    return info;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchMe().then((info) => {
      if (!alive) return;
      setMe(info);
      setChecked(true);
    });
    // Any 401 from the API (expired or revoked session) signs the page out.
    const onSignedOut = () => setMe(null);
    window.addEventListener("hestia:signed-out", onSignedOut);
    return () => {
      alive = false;
      window.removeEventListener("hestia:signed-out", onSignedOut);
    };
  }, []);

  const value = useMemo<Session>(
    () => ({
      me,
      checked,
      signIn: async (username, password) => {
        await post("/api/auth/login", { username, password });
        setMe(await fetchMe());
      },
      signOut: async () => {
        try {
          await post("/api/auth/logout", {});
        } finally {
          setCsrf("");
          setMe(null);
        }
      },
      can: (role) => (me ? RANK[me.user.role] >= RANK[role] : false),
    }),
    [me, checked],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}
