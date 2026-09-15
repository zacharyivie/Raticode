import raticodeIcon from "../assets/roundel.png";

export default function RaticodeMark({ className = "" }) {
  return (
    <img
      alt=""
      aria-hidden="true"
      className={`block shrink-0 ${className}`}
      draggable="false"
      src={raticodeIcon}
    />
  );
}
