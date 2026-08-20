import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AuthProvider, ProtectedRoute } from './lib/auth'
import { HeaderProvider } from './lib/pageHeader'
import { ToastProvider } from './lib/toast'
import { AlertsPage } from './pages/AlertsPage'
import { LoginPage } from './pages/LoginPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { StatusPage } from './pages/StatusPage'
import { StockDetailPage } from './pages/StockDetailPage'
import { WatchlistsPage } from './pages/WatchlistsPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <HeaderProvider>
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
                <Route path="/alerts" element={<AlertsPage />} />
                <Route path="/status" element={<StatusPage />} />
                <Route path="/stocks/:id" element={<StockDetailPage />} />
                <Route path="/" element={<Navigate to="/watchlists" replace />} />
              </Route>
              <Route path="*" element={<Navigate to="/watchlists" replace />} />
            </Routes>
          </HeaderProvider>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
