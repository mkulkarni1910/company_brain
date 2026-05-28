"use client";
import { MsalProvider } from "@azure/msal-react";
import { ReactNode } from "react";
import { msalInstance } from "@/lib/msal";

export function Providers({ children }: { children: ReactNode }) {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>;
}
