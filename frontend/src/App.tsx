import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import StreamTest from "./StreamTest";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Dashboard />
              </RequireAuth>
            }
          />
          {/* Phase 0 SSE infrastructure check — kept as a standalone route so
              streaming can be re-verified on the deployed box at any time. */}
          <Route
            path="/stream-test"
            element={
              <main className="mx-auto max-w-5xl px-6 py-8">
                <StreamTest />
              </main>
            }
          />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
