/** Typed wrappers around the auth endpoints. */
import { api, refreshRaw } from "./client";
import type { AuthResponse, RefreshResponse, User } from "../types";

export async function register(
  username: string,
  email: string,
  password: string,
): Promise<AuthResponse> {
  const res = await api.post<AuthResponse>("/auth/register/", {
    username,
    email,
    password,
  });
  return res.data;
}

export async function login(
  email: string,
  password: string,
): Promise<AuthResponse> {
  const res = await api.post<AuthResponse>("/auth/login/", { email, password });
  return res.data;
}

export async function refresh(): Promise<RefreshResponse> {
  const res = await refreshRaw.post<RefreshResponse>("/auth/refresh/");
  return res.data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout/");
}

export async function logoutAll(): Promise<void> {
  await api.post("/auth/logout-all/");
}

export async function fetchMe(): Promise<User> {
  const res = await api.get<User>("/auth/me/");
  return res.data;
}
