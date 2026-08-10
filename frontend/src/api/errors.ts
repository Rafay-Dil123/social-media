import { AxiosError } from "axios";
import type { ApiError } from "../types";

/** Turn any thrown value from the API into a human-readable message. */
export function toMessage(err: unknown, fallback = "Something went wrong."): string {
  if (err instanceof AxiosError && err.response?.data) {
    const data = err.response.data as ApiError;
    const detail = data.error?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      // Field errors -> first message we find.
      const first = Object.values(detail)[0];
      if (Array.isArray(first) && first.length) return first[0];
    }
  }
  return fallback;
}
