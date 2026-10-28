// Rem's rat face: round ears, a tapered snout, and whiskers.
// Lucide-compatible props so it can sit in the activity rail beside other icons.
export default function RatFaceIcon({ size = 24, strokeWidth = 1.5, ...props }) {
  const fine = strokeWidth * (2 / 3);
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M12 7.4C15.7 7.4 18.2 9.7 18.2 12.6C18.2 15.2 14.6 18.9 12.9 20.3Q12 21 11.1 20.3C9.4 18.9 5.8 15.2 5.8 12.6C5.8 9.7 8.3 7.4 12 7.4Z" />
      <path d="M6.1 10.2A3.4 3.4 0 1 1 9.2 7.9" />
      <path d="M17.9 10.2A3.4 3.4 0 1 0 14.8 7.9" />
      <path d="M5.3 7.3A1.6 1.6 0 0 1 7 5.8M18.7 7.3A1.6 1.6 0 0 0 17 5.8" strokeWidth={strokeWidth * 0.75} />
      <circle cx="9.6" cy="12.6" r="1" fill="currentColor" stroke="none" />
      <circle cx="14.4" cy="12.6" r="1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="19.4" r="1.05" fill="currentColor" stroke="none" />
      <path d="M9.4 16.3 4.2 15.3M9.7 17.4 4.8 18.7M14.6 16.3l5.2-1M14.3 17.4l4.9 1.3" strokeWidth={fine} />
    </svg>
  );
}
