import { create } from 'zustand'

interface UIState {
  darkMode: boolean
  locale: 'ko' | 'en'
  selectedStageOrder: number | null
  sidebarOpen: boolean
  apiKeyModalOpen: boolean
  toggleDarkMode: () => void
  setLocale: (l: 'ko' | 'en') => void
  selectStage: (order: number | null) => void
  setSidebarOpen: (open: boolean) => void
  setApiKeyModalOpen: (open: boolean) => void
}

export const useUIStore = create<UIState>((set) => ({
  darkMode: true,
  locale: 'ko',
  selectedStageOrder: null,
  sidebarOpen: true,
  apiKeyModalOpen: false,
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
  setLocale: (locale) => set({ locale }),
  selectStage: (order) => set({ selectedStageOrder: order }),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setApiKeyModalOpen: (open) => set({ apiKeyModalOpen: open }),
}))
