import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ExecutionPanel } from "@/components/ExecutionPanel";
import { makeState } from "./fixtures";

describe("ExecutionPanel — execution states", () => {
  it("shows a completed execution status", () => {
    const { execution } = makeState({
      execution: {
        simulation_status: "PASSED",
        would_revert: false,
        gas_estimate: "120000",
        policy_approved: true,
        execution_status: "completed",
        execution_id: "demo-exec-0000000000",
        transaction_hash: "0xdemo00000000000000000000000000000000000000000000000000000000",
        explorer_url: "https://sepolia.basescan.org/tx/0xdemo00",
        executed_through: "KeeperHub",
      },
    });
    render(<ExecutionPanel execution={execution} />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByTestId("execution-id")).toHaveTextContent("demo-exec-0000000000");
  });

  it("renders the transaction hash and a KeeperHub-only explorer link", () => {
    const { execution } = makeState({
      execution: {
        simulation_status: "PASSED",
        would_revert: false,
        gas_estimate: "120000",
        policy_approved: true,
        execution_status: "completed",
        execution_id: "demo-exec-0000000000",
        transaction_hash: "0xabc123",
        explorer_url: "https://sepolia.basescan.org/tx/0xabc123",
        executed_through: "KeeperHub",
      },
    });
    render(<ExecutionPanel execution={execution} />);
    expect(screen.getByTestId("transaction-hash")).toHaveTextContent("0xabc123");
    const link = screen.getByTestId("explorer-link");
    expect(link).toHaveAttribute("href", "https://sepolia.basescan.org/tx/0xabc123");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(screen.getByText("Executed through KeeperHub")).toBeInTheDocument();
  });

  it("shows an uncertain execution distinctly from completed or failed", () => {
    const { execution } = makeState({
      execution: {
        simulation_status: "PASSED",
        would_revert: false,
        gas_estimate: "120000",
        policy_approved: true,
        execution_status: "uncertain",
        execution_id: "exec-uncertain-1",
        transaction_hash: null,
        explorer_url: null,
        executed_through: "KeeperHub",
      },
    });
    render(<ExecutionPanel execution={execution} />);
    const pill = screen.getByTestId("status-pill");
    expect(pill).toHaveAttribute("data-kind", "uncertain");
    expect(pill).toHaveTextContent("Uncertain");
  });
});
