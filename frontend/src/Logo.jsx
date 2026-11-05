import light from "./assets/logo.png";
import dark from "./assets/logo-dark.png";

// The wordmark is dark green, so dark mode gets a variant with a light wordmark.
export default function Logo({ height = 36 }) {
  return (
    <picture>
      <source srcSet={dark} media="(prefers-color-scheme: dark)" />
      <img src={light} alt="AlphaRupee" height={height} className="logo" />
    </picture>
  );
}
