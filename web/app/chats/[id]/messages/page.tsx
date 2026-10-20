import { redirect } from "next/navigation";

/** The v3 address of the communications, kept so old links land. */
export default async function Messages({ params }: { params: Promise<{ id: string }> }) {
  redirect(`/chats/${(await params).id}/communications`);
}
