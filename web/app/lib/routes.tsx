import { RouteObject } from 'react-router-dom';
import App from '@/App';
import IntakePage from '@/app/dashboard/intake';
import NotePage from '@/app/dashboard/notes';
import SettingsPage from '@/app/dashboard/settings';
import { clientLoader } from '@/lib/loaders';

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <App />,
    children: [
      {
        path: "/",
        element: <IntakePage />,
        loader: clientLoader,
      },
      {
        path: "/notes",
        element: <NotePage />,
        loader: clientLoader,
      },
      {
        path: "/settings",
        element: <SettingsPage />,
      },
    ],
  },
];