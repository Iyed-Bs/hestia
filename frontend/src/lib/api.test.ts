import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, post, setCsrf } from "./api";

function respond(status: number, body: unknown) {
  // A fresh Response per call: a body can only be read once.
  return vi.fn().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
  );
}

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setCsrf("");
  });

  it("sends the CSRF token on state-changing requests only", async () => {
    const fetch = respond(200, { ok: true });
    vi.stubGlobal("fetch", fetch);
    setCsrf("token-123");

    await api("/api/state");
    await post("/api/commands", { kind: "set_mode", mode: "AUTO" });

    const getHeaders = fetch.mock.calls[0]?.[1]?.headers as Headers;
    const postHeaders = fetch.mock.calls[1]?.[1]?.headers as Headers;
    expect(getHeaders.has("X-CSRF-Token")).toBe(false);
    expect(postHeaders.get("X-CSRF-Token")).toBe("token-123");
    expect(postHeaders.get("Content-Type")).toBe("application/json");
  });

  it("surfaces the gateway's error message", async () => {
    vi.stubGlobal("fetch", respond(409, { detail: "Mode change refused while the H₂ alarm is latched" }));
    await expect(post("/api/commands", {})).rejects.toMatchObject({
      status: 409,
      message: "Mode change refused while the H₂ alarm is latched",
    });
  });

  it("reads the first validation message from a 422", async () => {
    vi.stubGlobal("fetch", respond(422, { detail: [{ msg: "Input should be less than or equal to 8.5", loc: ["value"] }] }));
    await expect(post("/api/commands", {})).rejects.toThrow("less than or equal to 8.5");
  });

  it("signs the page out when the session is gone", async () => {
    vi.stubGlobal("fetch", respond(401, { detail: "Not signed in" }));
    const signedOut = vi.fn();
    window.addEventListener("hestia:signed-out", signedOut);
    await expect(api("/api/state")).rejects.toBeInstanceOf(ApiError);
    expect(signedOut).toHaveBeenCalledOnce();
    window.removeEventListener("hestia:signed-out", signedOut);
  });

  it("does not treat a failed sign-in as a sign-out", async () => {
    vi.stubGlobal("fetch", respond(401, { detail: "Invalid credentials" }));
    const signedOut = vi.fn();
    window.addEventListener("hestia:signed-out", signedOut);
    await expect(post("/api/auth/login", {})).rejects.toBeInstanceOf(ApiError);
    expect(signedOut).not.toHaveBeenCalled();
    window.removeEventListener("hestia:signed-out", signedOut);
  });
});
