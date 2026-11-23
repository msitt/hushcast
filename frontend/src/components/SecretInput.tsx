import type { InputHTMLAttributes } from "react";

// API keys are not login credentials. A type="password" input makes password
// managers offer to save them on every form submit, so this is a text input
// masked with CSS and opted out of the common managers' autofill.
export function SecretInput({ className, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  return (
    <input
      {...props}
      type="text"
      className={className ? `secret-input ${className}` : "secret-input"}
      autoComplete="off"
      autoCorrect="off"
      autoCapitalize="off"
      spellCheck={false}
      data-bwignore="true"
      data-1p-ignore="true"
      data-lpignore="true"
      data-form-type="other"
    />
  );
}
