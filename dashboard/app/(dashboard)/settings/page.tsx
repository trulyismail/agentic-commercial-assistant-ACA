import { getSettings } from "@/lib/aca";
import { SettingsForm } from "./settings-form";

export default async function SettingsPage() {
  const { schema, values } = await getSettings();

  return (
    <div className="p-8">
      <header className="mb-8">
        <h1 className="font-display text-2xl font-semibold text-paper">Réglages</h1>
        <p className="mt-1 text-sm text-muted">
          Un champ laissé vide retombe sur la valeur <code className="font-mono text-paper-dim">.env</code> existante.
        </p>
      </header>
      <SettingsForm schema={schema} values={values} />
    </div>
  );
}
