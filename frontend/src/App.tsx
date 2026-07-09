import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAdmin, RequireAuth } from "./auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import NewEncounter from "./pages/NewEncounter";
import Workspace from "./pages/Workspace";
import AdminDashboard from "./pages/AdminDashboard";
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
          <Route
            path="/encounters/new"
            element={
              <RequireAuth>
                <NewEncounter />
              </RequireAuth>
            }
          />
          <Route
            path="/encounters/:id"
            element={
              <RequireAuth>
                <Workspace />
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAdmin>
                <AdminDashboard />
              </RequireAdmin>
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
