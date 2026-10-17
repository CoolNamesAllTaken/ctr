import { ApiError } from "@/lib/api";

export type ActionState =
  | { status: "idle" }
  | { status: "success"; message: string }
  | { status: "error"; message: string };

/** The backend's error message if it sent one, otherwise `fallback`. */
export function errorState(error: unknown, fallback: string): ActionState {
  if (error instanceof ApiError && typeof error.detail === "string") {
    return { status: "error", message: error.detail };
  }
  console.error(error);
  return { status: "error", message: fallback };
}
