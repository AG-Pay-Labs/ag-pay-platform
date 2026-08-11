export type UUID = string;
export type ISODateTime = string;
export type DecimalString = string;

export type AgentStatus = "pending" | "active" | "revoked";
export type AgentConnectionState = "pending" | "online" | "offline" | "revoked";
export type BillingProfileType = "personal" | "business";
export type BillingPeriod = "monthly" | "yearly";
export type PaymentMethodStatus = "active" | "disabled";
export type CartItemStatus = "proposed" | "approved" | "cancelled" | "purchased";
export type CheckoutExecutionStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "action_required"
  | "outcome_unknown";
export type PurchaseStatus = "completed" | "failed" | "refunded";
export type SubscriptionStatus = "active" | "cancelled" | "paused";
export type PaymentApprovalMode =
  | "always"
  | "subscriptions_only"
  | "above_amount"
  | "subscriptions_or_above_amount"
  | "never";

export interface Message {
  message: string;
}

export interface UserRegister {
  username: string;
  password: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface UserRead {
  id: UUID;
  username: string;
  is_active: boolean;
  created_at: ISODateTime;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: ISODateTime;
}

export interface AuthSession {
  user: UserRead;
  expires_at: ISODateTime;
}

export interface AgentCreate {
  name: string;
  description?: string | null;
}

export interface AgentRead {
  id: UUID;
  name: string;
  description: string | null;
  status: AgentStatus;
  connection_state: AgentConnectionState;
  instance_id: string | null;
  software_version: string | null;
  capabilities: string[];
  connected_at: ISODateTime | null;
  last_seen_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface AgentCreated extends AgentRead {
  pairing_token: string;
  pairing_expires_at: ISODateTime;
}

export interface PairingTokenResponse {
  pairing_token: string;
  pairing_expires_at: ISODateTime;
}

export interface AgentHandshake {
  pairing_token: string;
  instance_id: string;
  software_version?: string | null;
  capabilities?: string[];
}

export interface AgentTokenResponse {
  agent_id: UUID;
  agent_access_token: string;
  token_type: "bearer";
  expires_at: ISODateTime;
}

export interface AgentHeartbeatResponse {
  agent_id: UUID;
  connection_state: "online";
  server_time: ISODateTime;
}

export interface BillingAddress {
  line1: string;
  line2?: string | null;
  city: string;
  region?: string | null;
  postal_code: string;
  country: string;
}

export interface PersonalBillingDetails {
  type: "personal";
  full_name: string;
  email: string;
  phone?: string | null;
  address: BillingAddress;
}

export interface BusinessBillingDetails {
  type: "business";
  legal_name: string;
  vat_number: string;
  registration_number?: string | null;
  contact_name: string;
  email: string;
  phone?: string | null;
  address: BillingAddress;
}

export type BillingDetails = PersonalBillingDetails | BusinessBillingDetails;

export interface PaymentMethodCreate {
  display_name: string;
  provider: string;
  provider_payment_method_id: string;
  card_brand: string;
  card_last4: string;
  expiry_month: number;
  expiry_year: number;
  billing_details: BillingDetails;
}

export interface PaymentMethodRead {
  id: UUID;
  display_name: string;
  status: PaymentMethodStatus;
  provider: string;
  card_brand: string;
  card_last4: string;
  expiry_month: number;
  expiry_year: number;
  billing_profile_type: BillingProfileType;
  billing_details: BillingDetails;
  created_at: ISODateTime;
}

export interface AccountCredentialInput {
  email: string;
  password: string;
  login_url?: string | null;
}

export interface CheckoutRequest {
  adapter: string;
  checkout_url: string;
}

export interface CheckoutStatusTransitionRead {
  status: CheckoutExecutionStatus;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  occurred_at: ISODateTime;
}

export interface CheckoutExecutionRead {
  id: UUID;
  status: CheckoutExecutionStatus;
  attempt_count: number;
  approved_amount: DecimalString;
  currency: string;
  checkout_origin: string;
  error_code: string | null;
  error_message: string | null;
  merchant_order_reference: string | null;
  browserbase_session_id: string | null;
  submitted_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  status_history: CheckoutStatusTransitionRead[];
}

export interface CartItemCreate {
  title: string;
  description: string;
  product_url: string;
  merchant?: string | null;
  reason: string;
  quantity?: number;
  unit_price: DecimalString;
  currency: string;
  billing_period?: BillingPeriod | null;
  checkout?: CheckoutRequest | null;
  account: AccountCredentialInput;
}

export interface CartItemRead {
  id: UUID;
  agent_id: UUID;
  credential_id: UUID;
  selected_payment_method_id: UUID | null;
  title: string;
  description: string;
  product_url: string;
  merchant: string | null;
  reason: string;
  quantity: number;
  unit_price: DecimalString;
  total_amount: DecimalString;
  currency: string;
  billing_period: BillingPeriod | null;
  checkout_adapter: string | null;
  checkout_url: string | null;
  execution: CheckoutExecutionRead | null;
  status: CartItemStatus;
  decision_note: string | null;
  account_email: string;
  login_url: string | null;
  approved_at: ISODateTime | null;
  cancelled_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface CartApproval {
  payment_method_id: UUID;
  note?: string | null;
}

export interface CartCancellation {
  note?: string | null;
}

export interface CredentialRevealRequest {
  current_password: string;
}

/** Sensitive and ephemeral. Never put this object in persistent or query caches. */
export interface CredentialReveal {
  email: string;
  password: string;
  login_url: string | null;
}

export interface PurchaseComplete {
  amount: DecimalString;
  currency: string;
  provider_reference: string;
  receipt_url?: string | null;
  next_billing_at?: ISODateTime | null;
}

export interface SubscriptionRead {
  id: UUID;
  purchase_id: UUID;
  agent_id: UUID;
  title: string;
  billing_period: BillingPeriod;
  status: SubscriptionStatus;
  amount: DecimalString;
  currency: string;
  next_billing_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface PurchaseRead {
  id: UUID;
  cart_item_id: UUID;
  agent_id: UUID;
  payment_method_id: UUID;
  title: string;
  description: string;
  product_url: string;
  status: PurchaseStatus;
  amount: DecimalString;
  currency: string;
  provider_reference: string;
  merchant_order_reference: string | null;
  receipt_url: string | null;
  account_email: string;
  purchased_at: ISODateTime;
  subscription: SubscriptionRead | null;
}

export interface SubscriptionUpdate {
  status: SubscriptionStatus;
  /** Omitting this currently clears the value in the backend. */
  next_billing_at?: ISODateTime | null;
}

export interface PaymentPolicyRead {
  id: UUID;
  agent_id: UUID;
  mode: PaymentApprovalMode;
  threshold_amount: DecimalString | null;
  threshold_currency: string | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface PaymentPolicyUpdate {
  mode: PaymentApprovalMode;
  threshold_amount: DecimalString | null;
  threshold_currency: string | null;
}

export interface ValidationIssue {
  type: string;
  loc: Array<string | number>;
  msg: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface DetailErrorPayload {
  detail: string;
}

export interface ValidationErrorPayload {
  detail: ValidationIssue[];
}

export type ApiErrorPayload = DetailErrorPayload | ValidationErrorPayload;
