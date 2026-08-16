import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useConnectedWallet } from "@/lib/wallet";

// Stub only window.ethereum, never window itself - replacing the whole
// window object breaks React's own use of the real DOM in jsdom.
function stubProvider(accounts: string[], chainIdHex = "0x14a34" /* 84532 */) {
  const request = vi.fn().mockImplementation(({ method }: { method: string }) => {
    if (method === "eth_requestAccounts") return Promise.resolve(accounts);
    if (method === "eth_chainId") return Promise.resolve(chainIdHex);
    return Promise.reject(new Error(`unexpected method ${method}`));
  });
  Object.defineProperty(window, "ethereum", {
    value: { request, on: vi.fn(), removeListener: vi.fn() },
    configurable: true,
  });
  return request;
}

function removeProvider() {
  Object.defineProperty(window, "ethereum", { value: undefined, configurable: true });
}

describe("useConnectedWallet", () => {
  afterEach(() => {
    removeProvider();
  });

  it("requests accounts (read-only) and never a signing or sending method", async () => {
    const request = stubProvider(["0xAbC1230000000000000000000000000000dEf9AB"]);
    const { result } = renderHook(() => useConnectedWallet());

    await act(async () => {
      await result.current.connect();
    });

    await waitFor(() => expect(result.current.address).toBe("0xAbC1230000000000000000000000000000dEf9AB"));
    expect(request).toHaveBeenCalledWith({ method: "eth_requestAccounts" });
    expect(request).not.toHaveBeenCalledWith(expect.objectContaining({ method: expect.stringMatching(/sign|send/i) }));
  });

  it("reads the connected chain as a decimal chain id", async () => {
    stubProvider(["0xAbC1230000000000000000000000000000dEf9AB"], "0x14a34");
    const { result } = renderHook(() => useConnectedWallet());

    await act(async () => {
      await result.current.connect();
    });

    await waitFor(() => expect(result.current.chainId).toBe("84532"));
  });

  it("reports no provider instead of failing silently", () => {
    removeProvider();
    const { result } = renderHook(() => useConnectedWallet());
    expect(result.current.hasProvider).toBe(false);
  });

  it("disconnect clears both address and chain locally", async () => {
    stubProvider(["0xAbC1230000000000000000000000000000dEf9AB"]);
    const { result } = renderHook(() => useConnectedWallet());

    await act(async () => {
      await result.current.connect();
    });
    await waitFor(() => expect(result.current.address).not.toBeNull());

    act(() => result.current.disconnect());

    expect(result.current.address).toBeNull();
    expect(result.current.chainId).toBeNull();
  });
});
