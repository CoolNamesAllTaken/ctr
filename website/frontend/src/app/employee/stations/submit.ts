import { type FormEvent, startTransition } from "react";

/**
 * An onSubmit handler for forms with several buttons (save, move, delete),
 * each with name="intent". Submits via onSubmit rather than
 * action={formAction}, so React doesn't reset the form and wipe the
 * employee's edits, and asks first if the button has a data-confirm message.
 */
export function submitWithIntent(formAction: (formData: FormData) => void) {
  return (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const submitter = (e.nativeEvent as SubmitEvent).submitter;
    const confirmMessage = submitter?.dataset.confirm;
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    const formData = new FormData(e.currentTarget, submitter);
    startTransition(() => formAction(formData));
  };
}
