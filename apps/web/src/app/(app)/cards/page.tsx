import { redirect } from "next/navigation";

export default function CardsRedirect() {
  redirect("/payment-methods?tab=cards");
}
