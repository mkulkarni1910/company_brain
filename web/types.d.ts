import "react";
declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "cite-ref": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}
