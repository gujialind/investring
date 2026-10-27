export interface CashTransferCreate {
  from_platform: string;
  to_platform: string;
  amount: number;
  cross_day?: boolean;
  transfer_date: string;
  notes?: string;
}

export interface CashTransferResponse {
  transfer_group: string;
  from_platform: string;
  to_platform: string;
  amount: number;
  cross_day: boolean;
  sell_trade_id: number;
  buy_trade_id: number;
  sell_status: string;
  buy_status: string;
  transfer_date: string;
}

export interface CashTransferCancelResponse {
  message: string;
  transfer_group: string;
  from_platform: string;
  to_platform: string;
  amount: number;
  sell_status: string;
  buy_status: string;
  transfer_date: string;
}

export interface CashTransferListItem {
  transfer_group: string;
  from_platform: string;
  to_platform: string;
  amount: number;
  cross_day: boolean;
  sell_status: string;
  buy_status: string;
  transfer_date: string;
  sell_confirm_date?: string;
  buy_confirm_date?: string;
  notes?: string;
}
