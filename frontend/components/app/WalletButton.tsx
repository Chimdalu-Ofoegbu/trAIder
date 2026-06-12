"use client";

// =============================================================================
// frontend/components/app/WalletButton.tsx — topbar connect/account button.
// Not connected → design-matched "Connect Wallet" primary button.
// Connected → wallet-chip showing the address (opens the account modal).
// =============================================================================

import { ConnectButton } from "@rainbow-me/rainbowkit";

export function WalletButton() {
  return (
    <ConnectButton.Custom>
      {({ account, mounted, openConnectModal, openAccountModal }) =>
        mounted && account ? (
          <button
            className="wallet-chip"
            onClick={openAccountModal}
            style={{ cursor: "pointer", background: "transparent" }}
          >
            <span className="dot dot-live" />
            <span className="wallet-addr">{account.displayName}</span>
          </button>
        ) : (
          <button className="btn btn-primary btn-sm" onClick={openConnectModal}>
            Connect Wallet
          </button>
        )
      }
    </ConnectButton.Custom>
  );
}
