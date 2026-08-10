/**
 * Axios instance with in-memory access token + transparent refresh.
 *
 * - The access token lives in a module variable (memory only) — never in
 *   localStorage, so it is not readable by injected scripts across reloads.
 * - The refresh token is an httpOnly cookie the browser attaches automatically
 *   (withCredentials), so JS never touches it.
 * - On a 401, the response interceptor calls /auth/refresh once, updates the
 *   access token, and replays the failed request. Concurrent 401s share a
 *   single in-flight refresh.
 */
import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from "axios";
import type { RefreshResponse } from "../types";

const BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

// --- in-memory access token ------------------------------------------------
let accessToken: string | null = null;
export const getAccessToken = () => accessToken;
export const setAccessToken = (token: string | null) => {
  accessToken = token;
};

// Called when refresh fails so the app can drop to the login screen.
let onAuthFailure: () => void = () => {};
export const setOnAuthFailure = (cb: () => void) => {
  onAuthFailure = cb;
};

// --- axios instances -------------------------------------------------------
export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  withCredentials: true, // send/receive the httpOnly refresh cookie
});

// A bare client for the refresh call itself, so it never triggers the
// response interceptor (which would recurse). Also used for the app's
// initial "am I already logged in?" bootstrap.
export const refreshRaw: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
});
const refreshClient = refreshRaw;

// --- request: attach the bearer token --------------------------------------
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

// --- response: refresh-and-retry on 401 ------------------------------------
let refreshPromise: Promise<string> | null = null;

/**
 * Refresh the access token, coalescing concurrent callers into ONE network
 * call. Every caller — the 401 interceptor AND the app's initial bootstrap —
 * must go through this, otherwise two parallel /auth/refresh calls race and,
 * with refresh-token rotation, the second one 401s and kills the session.
 */
export async function refreshAccessToken(): Promise<string> {
  // Coalesce concurrent refreshes into one network call.
  if (!refreshPromise) {
    refreshPromise = refreshClient
      .post<RefreshResponse>("/auth/refresh/")
      .then((res) => {
        const token = res.data.access_token;
        setAccessToken(token);
        return token;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined;

    const isAuthEndpoint = original?.url?.includes("/auth/");
    if (
      error.response?.status === 401 &&
      original &&
      !original._retried &&
      !isAuthEndpoint
    ) {
      original._retried = true;
      try {
        const token = await refreshAccessToken();
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      } catch {
        setAccessToken(null);
        onAuthFailure();
      }
    }
    return Promise.reject(error);
  },
);
