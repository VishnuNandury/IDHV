import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './auth'
import { PreviewProvider } from './components/DocumentPreview'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <PreviewProvider>
          <App />
        </PreviewProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
