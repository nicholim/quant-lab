import { defineConfig } from "vite";

// Static, no-backend showcase site. Output goes to dist/ (Netlify publish dir).
export default defineConfig({
  root: ".",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
