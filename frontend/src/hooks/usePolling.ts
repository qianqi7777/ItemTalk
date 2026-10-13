import { useEffect } from 'react';

/** 单次查询结束后再调度下一次，防止慢网络下轮询请求叠加。 */
export function usePolling(
  action: () => Promise<void>,
  enabled: boolean,
  interval = 1500,
) {
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await action();
      } finally {
        if (active) timer = setTimeout(() => void poll(), interval);
      }
    };
    timer = setTimeout(() => void poll(), interval);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [action, enabled, interval]);
}
