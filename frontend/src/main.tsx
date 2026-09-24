import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// No <StrictMode>: its double mount breaks drei <Html> labels under React 19 (the first label never reappears)
createRoot(document.getElementById("root")!).render(<App />);
