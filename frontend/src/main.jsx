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

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppCrashBoundary>
      <RouterProvider router={router} />
    </AppCrashBoundary>
  </React.StrictMode>,
);
