import { createContext, useContext } from 'react';
import type { User } from '../types/api';

export const SessionContext = createContext<{
  user: User | null;
  refresh: () => Promise<void>;
}>({ user: null, refresh: async () => {} });
export const useSession = () => useContext(SessionContext);
