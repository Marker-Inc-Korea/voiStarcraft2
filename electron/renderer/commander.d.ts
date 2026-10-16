export interface CommanderDocument { [key: string]: unknown; }
export interface CommanderBridge {
  status(): Promise<CommanderDocument>;
  capabilities(): Promise<CommanderDocument>;
  sendCommand(text: string, responseLanguage?: "ko" | "en"): Promise<CommanderDocument>;
}
declare global { interface Window { commander: CommanderBridge; } }
