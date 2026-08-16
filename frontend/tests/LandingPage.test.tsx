import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import LandingPage from "@/app/page";

describe("Landing page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the hero and links the primary CTA to the app, never straight into a transaction", () => {
    render(<LandingPage />);
    expect(screen.getByText("AEGIS")).toBeInTheDocument();
    expect(screen.getByText("Execution-Aware DeFi Guardian")).toBeInTheDocument();

    const launchCta = screen.getByTestId("launch-cta");
    expect(launchCta).toHaveAttribute("href", "/app");

    const howItWorksCta = screen.getByTestId("how-it-works-cta");
    expect(howItWorksCta).toHaveAttribute("href", "#how-it-works");
  });

  it("never contacts the backend — it is a fully static page", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<LandingPage />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("names KeeperHub as the execution layer without overclaiming Hermes's current role", () => {
    render(<LandingPage />);
    expect(screen.getByText(/onchain execution and reliability layer/i)).toBeInTheDocument();
  });
});
