// Hestia's mark: a flame (the hearth) whose core is a hydrogen molecule.
// One solid ember colour: it follows the theme's brand token.
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="logo">
      <path
        d="M16 2c1.5 4.6 7.5 7.6 7.5 15A7.5 7.5 0 0 1 16 24.5 7.5 7.5 0 0 1 8.5 17c0-3.5 1.8-5.7 3.4-7.4.3 2.2 1.2 3.6 2.4 4.4C14.3 9.3 14.6 5.5 16 2Z"
        fill="var(--brand)"
      />
      <circle cx="13.3" cy="18.4" r="2.1" fill="var(--bg)" />
      <circle cx="18.7" cy="18.4" r="2.1" fill="var(--bg)" />
      <rect x="14.6" y="17.8" width="2.8" height="1.2" fill="var(--bg)" />
      <path d="M8 28.5h16" stroke="var(--text-faint)" strokeWidth="1.6" />
    </svg>
  );
}
