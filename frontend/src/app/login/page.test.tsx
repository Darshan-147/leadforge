import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { router, auth, createClient } = vi.hoisted(() => {
  const auth = { getUser: vi.fn(), signInWithPassword: vi.fn() };
  return {
    router: { replace: vi.fn(), refresh: vi.fn() },
    auth,
    createClient: vi.fn(() => ({ auth })),
  };
});

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/supabase/client", () => ({ createClient }));

import LoginPage from "./page";

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.getUser.mockResolvedValue({ data: { user: null } });
  });

  it("shows a Supabase sign-in error instead of navigating", async () => {
    auth.signInWithPassword.mockResolvedValue({ error: { message: "Invalid credentials" } });
    render(<LoginPage />);

    await userEvent.type(screen.getByPlaceholderText("you@example.com"), "person@example.com");
    await userEvent.type(screen.getByPlaceholderText("Password"), "secret1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("submits credentials and refreshes the authenticated route on success", async () => {
    auth.signInWithPassword.mockResolvedValue({ error: null });
    render(<LoginPage />);

    await userEvent.type(screen.getByPlaceholderText("you@example.com"), "person@example.com");
    await userEvent.type(screen.getByPlaceholderText("Password"), "secret1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: "person@example.com", password: "secret1" }));
    expect(router.replace).toHaveBeenCalledWith("/dashboard");
    expect(router.refresh).toHaveBeenCalled();
  });
});
