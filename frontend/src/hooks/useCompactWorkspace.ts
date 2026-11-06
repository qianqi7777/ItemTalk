import { useEffect, useState } from 'react';

// A phone in landscape still needs the task layout rather than a desktop sidebar.
const query = '(max-width: 800px), (max-width: 1000px) and (max-height: 500px)';

export function useCompactWorkspace(manageViewport = true) {
  const [compact, setCompact] = useState(
    () => window.matchMedia(query).matches,
  );
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!compact || !manageViewport) return;
    const update = () => {
      const viewport = window.visualViewport;
      const height = viewport?.height ?? window.innerHeight;
      const offset = viewport?.offsetTop ?? 0;
      document.documentElement.dataset.taskKeyboard = String(
        window.innerHeight - height - offset > 120,
      );
      document.documentElement.dataset.taskShort = String(height <= 500);
      document.documentElement.style.setProperty(
        '--task-viewport-height',
        `${height}px`,
      );
      document.documentElement.style.setProperty(
        '--task-viewport-top',
        `${offset}px`,
      );
      document.documentElement.style.setProperty(
        '--task-keyboard-bottom',
        `${Math.max(0, window.innerHeight - height - offset)}px`,
      );
    };
    update();
    window.visualViewport?.addEventListener('resize', update);
    window.visualViewport?.addEventListener('scroll', update);
    window.addEventListener('resize', update);
    return () => {
      window.visualViewport?.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
      delete document.documentElement.dataset.taskShort;
      delete document.documentElement.dataset.taskKeyboard;
      document.documentElement.style.removeProperty('--task-viewport-height');
      document.documentElement.style.removeProperty('--task-viewport-top');
      document.documentElement.style.removeProperty('--task-keyboard-bottom');
    };
  }, [compact, manageViewport]);
  return compact;
}
