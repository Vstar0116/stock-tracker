import { QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LoadingText } from './components/ui'
import { AuthProvider, ProtectedRoute } from './lib/auth'
import { HeaderProvider } from './lib/pageHeader'
import { queryClient } from './lib/queryClient'
import { ToastProvider } from './lib/toast'

// Route-level code splitting -- these used to be static imports at the top
// of this file, so every page (including StockDetailPage, which alone
// pulls in the TradingView embed script logic) shipped in the initial
// bundle regardless of which page a visit actually landed on.
const AlertsPage = lazy(() => import('./pages/AlertsPage').then((m) => ({ default: m.AlertsPage })))
const CustomScanPage = lazy(() => import('./pages/CustomScanPage').then((m) => ({ default: m.CustomScanPage })))
const LoginPage = lazy(() => import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const ScreenerPage = lazy(() => import('./pages/ScreenerPage').then((m) => ({ default: m.ScreenerPage })))
const StatusPage = lazy(() => import('./pages/StatusPage').then((m) => ({ default: m.StatusPage })))
const StockDetailPage = lazy(() => import('./pages/StockDetailPage').then((m) => ({ default: m.StockDetailPage })))
const WatchlistsPage = lazy(() => import('./pages/WatchlistsPage').then((m) => ({ default: m.WatchlistsPage })))

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <ToastProvider>
              <HeaderProvider>
                <Suspense fallback={<LoadingText />}>
                  <Routes>
                    <Route path="/login" element={<LoginPage />} />
                    <Route
                      element={
                        <ProtectedRoute>
                          <AppShell />
                        </ProtectedRoute>
                      }
                    >
                      <Route path="/watchlists" element={<WatchlistsPage />} />
                      <Route path="/screener" element={<ScreenerPage />} />
                      <Route path="/scan" element={<CustomScanPage />} />
                      <Route path="/alerts" element={<AlertsPage />} />
                      <Route path="/status" element={<StatusPage />} />
                      <Route path="/stocks/:id" element={<StockDetailPage />} />
                      <Route path="/" element={<Navigate to="/watchlists" replace />} />
                    </Route>
                    <Route path="*" element={<Navigate to="/watchlists" replace />} />
                  </Routes>
                </Suspense>
              </HeaderProvider>
            </ToastProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
