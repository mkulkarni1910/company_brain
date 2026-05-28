import { PublicClientApplication, Configuration } from "@azure/msal-browser";

const config: Configuration = {
  auth: {
    clientId: process.env.NEXT_PUBLIC_AZURE_CLIENT_ID!,
    authority: `https://login.microsoftonline.com/${process.env.NEXT_PUBLIC_AZURE_TENANT_ID}`,
    redirectUri: typeof window !== "undefined" ? window.location.origin : "",
  },
  cache: { cacheLocation: "sessionStorage" },
};

export const msalInstance = new PublicClientApplication(config);
export const apiScope = process.env.NEXT_PUBLIC_AZURE_API_SCOPE!;
