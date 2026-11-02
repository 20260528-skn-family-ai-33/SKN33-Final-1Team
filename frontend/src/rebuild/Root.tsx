import { BrowserRouter } from 'react-router';
import App from '../App.tsx';
import { AuthProvider } from '../auth.tsx';
import '../app.css';
import './fairweather.css';

// Each profile owns its session and API contract; never mix auth providers.
export default function RebuildRoot() {
  return <div className="rebuild-front-v2"><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></div>;
}
