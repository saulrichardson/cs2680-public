export function ThemeScript() {
  const code = `try{var t=localStorage.getItem('mc-theme');if(t==='light'||t==='dark'){document.documentElement.dataset.theme=t}}catch(e){}`;
  // biome-ignore lint/security/noDangerouslySetInnerHtml: static string, no user input
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
