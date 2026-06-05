import twilio from "twilio";

export interface TwilioRequestValidator {
  validate(input: {
    signature: string | undefined;
    url: string;
    params: Record<string, string>;
  }): boolean;
}

export class DefaultTwilioRequestValidator implements TwilioRequestValidator {
  constructor(private readonly authToken: string) {}

  validate(input: {
    signature: string | undefined;
    url: string;
    params: Record<string, string>;
  }): boolean {
    if (!input.signature?.trim()) {
      return false;
    }

    return twilio.validateRequest(
      this.authToken,
      input.signature.trim(),
      input.url,
      input.params
    );
  }
}
