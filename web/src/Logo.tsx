/** Job binoculars: a briefcase whose lenses look for roles. Same art as desktop/resources/logo-mark.svg. */
export default function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" aria-hidden="true" className="logo-mark">
      <rect x="44" y="18" width="32" height="20" rx="5" fill="none" stroke="var(--ink)" strokeWidth="7" />
      <rect x="17" y="40" width="94" height="64" rx="10" fill="var(--ink)" />
      <rect x="9" y="32" width="94" height="64" rx="10" fill="var(--lilac)" stroke="var(--ink)" strokeWidth="7" />
      <circle cx="36" cy="63" r="17" fill="#fff" stroke="var(--ink)" strokeWidth="7" />
      <circle cx="76" cy="63" r="17" fill="#fff" stroke="var(--ink)" strokeWidth="7" />
      <circle cx="41" cy="59" r="6" fill="#111" />
      <circle cx="81" cy="59" r="6" fill="#111" />
      <rect x="51" y="58" width="10" height="10" fill="var(--ink)" />
    </svg>
  )
}
