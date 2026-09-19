// WXT exposes WXT_*/VITE_* environment variables to the bundle. Declared here
// because .wxt/types/globals.d.ts is generated and only covers WXT's built-ins.
interface ImportMetaEnv {
  readonly WXT_BACKEND_URL?: string;
}
