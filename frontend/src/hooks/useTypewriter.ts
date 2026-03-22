import { useState, useRef, useEffect } from 'react';

export function useTypewriter(text: string, active: boolean, speed = 16) {
  const [displayed, setDisplayed] = useState('');
  const [done, setDone] = useState(false);
  const indexRef = useRef(0);
  const prevText = useRef('');

  useEffect(() => {
    if (!active) { setDisplayed(text); setDone(true); return; }
    if (text === prevText.current) return;
    prevText.current = text;
    indexRef.current = 0;
    setDisplayed('');
    setDone(false);

    const tick = () => {
      indexRef.current += 1;
      setDisplayed(text.slice(0, indexRef.current));
      if (indexRef.current < text.length) setTimeout(tick, speed);
      else setDone(true);
    };
    setTimeout(tick, speed);
  }, [text, active, speed]);

  return { displayed, done };
}
