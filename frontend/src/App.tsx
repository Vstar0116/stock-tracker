import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { Scene3D } from './components/Scene3D'
import { AuthProvider, ProtectedRoute } from './lib/auth'
import { HeaderProvider } from './lib/pageHeader'
import { ToastProvider } from './lib/toast'
import { AlertsPage } from './pages/AlertsPage'
import { AlertWizardPage } from './pages/AlertWizardPage'
import { CustomScanPage } from './pages/CustomScanPage'
import { DashboardPage } from './pages/DashboardPage'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { PortfolioPage } from './pages/PortfolioPage'
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
            <Scene3D />
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route
                element={
                  <ProtectedRoute>
                    <AppShell />
                  </ProtectedRoute>
                }
              >
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/watchlists" element={<WatchlistsPage />} />
                <Route path="/portfolio" element={<PortfolioPage />} />
                <Route path="/screener" element={<ScreenerPage />} />
                <Route path="/alert-wizard" element={<AlertWizardPage />} />
                <Route path="/scan" element={<CustomScanPage />} />
                <Route path="/alerts" element={<AlertsPage />} />
                <Route path="/status" element={<StatusPage />} />
                <Route path="/stocks/:id" element={<StockDetailPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </HeaderProvider>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
