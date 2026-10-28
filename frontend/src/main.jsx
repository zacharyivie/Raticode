import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import { AppCrashBoundary, RouteCrashPage } from "./components/AppCrashBoundary.jsx";
import { installGoferApiFetchAuth } from "./lib/api.js";
const App = lazy(() => import("./pages/App.jsx"));
import "./styles/index.css";

installGoferApiFetchAuth();

const router = createHashRouter([
  {
    path: "/",
    element: (
      <AppCrashBoundary>
        <Suspense fallback={null}>
          <App />
        </Suspense>
      </AppCrashBoundary>
    ),
    errorElement: <RouteCrashPage />,
  },
]);

// Vite re-runs this entry during hot updates. Reuse the existing React root so an
// edit never leaves an old copy of the app running against the same page.
const root = import.meta.hot?.data.root ?? ReactDOM.createRoot(document.getElementById("root"));
if (import.meta.hot) {
  import.meta.hot.data.root = root;
  import.meta.hot.dispose(() => router.dispose());
}

root.render(
  <React.StrictMode>
    <AppCrashBoundary>
      <RouterProvider router={router} />
    </AppCrashBoundary>
  </React.StrictMode>,
);
